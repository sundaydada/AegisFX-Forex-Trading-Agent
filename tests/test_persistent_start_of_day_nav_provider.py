"""Red contract for the persistent start-of-day NAV baseline provider.

Defines execution.persistent_start_of_day_nav_provider
.PersistentStartOfDayNavProvider: one immutable first-valid NAV
observation persisted per (account_id, account_currency, UTC calendar
date), following the PersistentHighWaterDrawdownProvider conventions.

The future 2% daily-loss gate (not implemented in this slice) will
consume the baseline as:

    daily_loss_fraction = max(
        0.0, (start_of_day_nav - current_nav) / start_of_day_nav
    )

and must block new exposure when daily_loss_fraction >= 0.02.
"""

import importlib
import math
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone

import pytest


_DAY_ONE_MORNING = datetime(2026, 7, 16, 9, 0, 0, tzinfo=timezone.utc)
_DAY_ONE_EVENING = datetime(2026, 7, 16, 18, 0, 0, tzinfo=timezone.utc)
_DAY_TWO_MORNING = datetime(2026, 7, 17, 9, 0, 0, tzinfo=timezone.utc)

_DAY_ONE = "2026-07-16"
_DAY_TWO = "2026-07-17"


def _daily_loss_fraction(start_of_day_nav, current_nav):
    return max(
        0.0,
        (start_of_day_nav - current_nav) / start_of_day_nav,
    )


def _clock(*times):
    remaining = list(times)

    def now():
        if len(remaining) > 1:
            return remaining.pop(0)
        return remaining[0]

    return now


def _snapshot(*, nav=100_000.0, currency="USD"):
    from brokers.broker_interface import AccountSnapshot

    return AccountSnapshot(
        nav=nav,
        balance=100_000.0,
        currency=currency,
        margin_available=95_000.0,
    )


def _make_provider(db_path, *, account_id="TEST-ACCOUNT", clock=None,
                   times=(_DAY_ONE_MORNING,)):
    from execution.persistent_start_of_day_nav_provider import (
        PersistentStartOfDayNavProvider,
    )

    return PersistentStartOfDayNavProvider(
        db_path=db_path,
        account_id=account_id,
        clock=clock if clock is not None else _clock(*times),
    )


def _read_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        try:
            cursor = conn.execute(
                "SELECT account_id, account_currency, utc_date,"
                " start_of_day_nav"
                " FROM start_of_day_nav"
                " ORDER BY account_id, account_currency, utc_date"
            )
        except sqlite3.OperationalError:
            return []
        return cursor.fetchall()
    finally:
        conn.close()


def test_provider_module_import_is_safe():
    before = set(sys.modules)

    module = importlib.import_module(
        "execution.persistent_start_of_day_nav_provider"
    )

    newly_loaded = set(sys.modules) - before
    new_roots = {name.partition(".")[0] for name in newly_loaded}
    assert module.__name__ == (
        "execution.persistent_start_of_day_nav_provider"
    )
    assert not (new_roots & {"streamlit", "market_data", "dotenv"})
    assert "dashboard.app" not in newly_loaded
    assert "brokers.oanda_broker" not in newly_loaded


def test_first_valid_observation_persists_baseline_and_loss_is_zero(
    tmp_path,
):
    db_path = str(tmp_path / "sod-nav.db")

    with closing(_make_provider(db_path, times=(_DAY_ONE_MORNING,))) as p:
        baseline = p.get_start_of_day_nav(_snapshot(nav=100_000.0))

    assert type(baseline) is float
    assert baseline == 100_000.0
    assert _daily_loss_fraction(baseline, 100_000.0) == 0.0
    assert _read_rows(db_path) == [
        ("TEST-ACCOUNT", "USD", _DAY_ONE, 100_000.0),
    ]


def test_same_day_observations_return_original_immutable_baseline(
    tmp_path,
):
    db_path = str(tmp_path / "sod-nav.db")

    with closing(
        _make_provider(
            db_path,
            times=(_DAY_ONE_MORNING, _DAY_ONE_EVENING, _DAY_ONE_EVENING),
        )
    ) as p:
        assert p.get_start_of_day_nav(_snapshot(nav=100_000.0)) == 100_000.0
        assert p.get_start_of_day_nav(_snapshot(nav=105_000.0)) == 100_000.0
        assert p.get_start_of_day_nav(_snapshot(nav=95_000.0)) == 100_000.0

    assert _read_rows(db_path) == [
        ("TEST-ACCOUNT", "USD", _DAY_ONE, 100_000.0),
    ]


def test_new_utc_date_creates_new_baseline(tmp_path):
    db_path = str(tmp_path / "sod-nav.db")

    with closing(
        _make_provider(
            db_path,
            times=(_DAY_ONE_MORNING, _DAY_TWO_MORNING),
        )
    ) as p:
        assert p.get_start_of_day_nav(_snapshot(nav=100_000.0)) == 100_000.0
        assert p.get_start_of_day_nav(_snapshot(nav=97_000.0)) == 97_000.0

    assert _read_rows(db_path) == [
        ("TEST-ACCOUNT", "USD", _DAY_ONE, 100_000.0),
        ("TEST-ACCOUNT", "USD", _DAY_TWO, 97_000.0),
    ]


def test_baseline_survives_close_and_reopen(tmp_path):
    db_path = str(tmp_path / "sod-nav.db")

    with closing(_make_provider(db_path, times=(_DAY_ONE_MORNING,))) as p:
        assert p.get_start_of_day_nav(_snapshot(nav=100_000.0)) == 100_000.0

    with closing(_make_provider(db_path, times=(_DAY_ONE_EVENING,))) as p:
        baseline = p.get_start_of_day_nav(_snapshot(nav=95_000.0))

    assert baseline == 100_000.0
    assert _daily_loss_fraction(baseline, 95_000.0) == pytest.approx(0.05)
    assert _read_rows(db_path) == [
        ("TEST-ACCOUNT", "USD", _DAY_ONE, 100_000.0),
    ]


def test_daily_loss_formula_blocks_new_exposure_at_two_percent(tmp_path):
    db_path = str(tmp_path / "sod-nav.db")

    with closing(_make_provider(db_path, times=(_DAY_ONE_MORNING,))) as p:
        baseline = p.get_start_of_day_nav(_snapshot(nav=100_000.0))

    assert _daily_loss_fraction(baseline, 98_000.01) < 0.02
    assert _daily_loss_fraction(baseline, 98_000.0) >= 0.02
    assert _daily_loss_fraction(baseline, 97_000.0) >= 0.02
    assert _daily_loss_fraction(baseline, 101_000.0) == 0.0


def test_account_and_currency_keys_are_isolated(tmp_path):
    db_path = str(tmp_path / "sod-nav.db")

    with closing(
        _make_provider(
            db_path,
            account_id="ACCOUNT-A",
            times=(_DAY_ONE_MORNING, _DAY_ONE_EVENING, _DAY_ONE_EVENING),
        )
    ) as account_a:
        assert account_a.get_start_of_day_nav(
            _snapshot(nav=100_000.0, currency="USD")
        ) == 100_000.0
        assert account_a.get_start_of_day_nav(
            _snapshot(nav=80_000.0, currency="EUR")
        ) == 80_000.0
        assert account_a.get_start_of_day_nav(
            _snapshot(nav=95_000.0, currency="USD")
        ) == 100_000.0

    with closing(
        _make_provider(
            db_path,
            account_id="ACCOUNT-B",
            times=(_DAY_ONE_EVENING,),
        )
    ) as account_b:
        assert account_b.get_start_of_day_nav(
            _snapshot(nav=50_000.0, currency="USD")
        ) == 50_000.0

    assert _read_rows(db_path) == [
        ("ACCOUNT-A", "EUR", _DAY_ONE, 80_000.0),
        ("ACCOUNT-A", "USD", _DAY_ONE, 100_000.0),
        ("ACCOUNT-B", "USD", _DAY_ONE, 50_000.0),
    ]


def test_invalid_snapshot_evidence_fails_closed_without_mutation(tmp_path):
    db_path = str(tmp_path / "sod-nav.db")

    with closing(
        _make_provider(
            db_path,
            times=(_DAY_ONE_MORNING, _DAY_ONE_EVENING),
        )
    ) as p:
        assert p.get_start_of_day_nav(_snapshot(nav=100_000.0)) == 100_000.0
        baseline_rows = _read_rows(db_path)
        assert len(baseline_rows) == 1

        invalid_snapshots = [object(), None]
        for invalid_nav in (
            True,
            None,
            "100000",
            math.nan,
            math.inf,
            -math.inf,
            0.0,
            -1.0,
        ):
            invalid_snapshots.append(_snapshot(nav=invalid_nav))
        for invalid_currency in (None, "", "US", 123):
            invalid_snapshots.append(_snapshot(currency=invalid_currency))

        for invalid_snapshot in invalid_snapshots:
            with pytest.raises(ValueError):
                p.get_start_of_day_nav(invalid_snapshot)

    assert _read_rows(db_path) == baseline_rows


def test_invalid_account_id_and_naive_clock_fail_closed(tmp_path):
    db_file = tmp_path / "never-created.db"
    for invalid_account_id in (None, True, 123, "", "   "):
        with pytest.raises(ValueError):
            _make_provider(
                str(db_file),
                account_id=invalid_account_id,
            )
        assert not db_file.exists()

    db_path = str(tmp_path / "naive-clock.db")
    naive_clock = lambda: datetime(2026, 7, 16, 9, 0, 0)  # noqa: E731
    with closing(_make_provider(db_path, clock=naive_clock)) as p:
        with pytest.raises(ValueError):
            p.get_start_of_day_nav(_snapshot(nav=100_000.0))
    assert _read_rows(db_path) == []


def test_incompatible_existing_schema_fails_closed_without_recreation(
    tmp_path,
):
    db_path = str(tmp_path / "poisoned.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE start_of_day_nav (wrong_column TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises((sqlite3.Error, ValueError)):
        with closing(_make_provider(db_path)) as p:
            p.get_start_of_day_nav(_snapshot(nav=100_000.0))

    conn = sqlite3.connect(db_path)
    try:
        columns = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(start_of_day_nav)"
            )
        ]
    finally:
        conn.close()
    assert columns == ["wrong_column"]


def test_all_databases_remain_under_tmp_path(tmp_path):
    db_path = str(tmp_path / "sod-nav.db")

    with closing(_make_provider(db_path, times=(_DAY_ONE_MORNING,))) as p:
        p.get_start_of_day_nav(_snapshot(nav=100_000.0))

    assert (tmp_path / "sod-nav.db").exists()
    assert db_path.startswith(str(tmp_path))
