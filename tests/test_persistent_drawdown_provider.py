import importlib
import inspect
import math
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone

import pytest


_T0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 7, 16, 12, 5, 0, tzinfo=timezone.utc)
_T2 = datetime(2026, 7, 16, 12, 10, 0, tzinfo=timezone.utc)


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
                   times=(_T0,)):
    from execution.persistent_drawdown_provider import (
        PersistentHighWaterDrawdownProvider,
    )

    return PersistentHighWaterDrawdownProvider(
        db_path=db_path,
        account_id=account_id,
        clock=clock if clock is not None else _clock(*times),
    )


def _read_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        try:
            cursor = conn.execute(
                "SELECT account_id, account_currency, high_water_nav,"
                " initialized_at, updated_at"
                " FROM drawdown_high_water"
                " ORDER BY account_id, account_currency"
            )
        except sqlite3.OperationalError:
            return []
        return cursor.fetchall()
    finally:
        conn.close()


def _corrupt_column(db_path, column, value):
    assert column in (
        "high_water_nav",
        "initialized_at",
        "updated_at",
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            f"UPDATE drawdown_high_water SET {column} = ?",
            (value,),
        )
        conn.commit()
    finally:
        conn.close()


def test_provider_module_import_is_safe():
    before = set(sys.modules)

    module = importlib.import_module(
        "execution.persistent_drawdown_provider"
    )

    newly_loaded = set(sys.modules) - before
    new_roots = {name.partition(".")[0] for name in newly_loaded}
    assert module.__name__ == "execution.persistent_drawdown_provider"
    assert not (new_roots & {"streamlit", "market_data", "dotenv"})
    assert "dashboard.app" not in newly_loaded
    assert "brokers.oanda_broker" not in newly_loaded


def test_invalid_account_identity_fails_before_database_creation(tmp_path):
    db_file = tmp_path / "never-created.db"

    for invalid_account_id in (None, True, 123, "", "   "):
        with pytest.raises(ValueError):
            _make_provider(
                str(db_file),
                account_id=invalid_account_id,
            )
        assert not db_file.exists()


def test_first_valid_observation_bootstraps_baseline_and_returns_zero(
    tmp_path,
):
    db_path = str(tmp_path / "drawdown.db")

    with closing(
        _make_provider(
            db_path,
            account_id="  TEST-ACCOUNT  ",
            times=(_T0,),
        )
    ) as provider:
        result = provider.get_drawdown_fraction(_snapshot(nav=100_000.0))

    assert type(result) is float
    assert result == 0.0
    assert _read_rows(db_path) == [
        (
            "TEST-ACCOUNT",
            "USD",
            100_000.0,
            _T0.isoformat(),
            _T0.isoformat(),
        ),
    ]


def test_equal_nav_returns_zero_and_preserves_row(tmp_path):
    db_path = str(tmp_path / "drawdown.db")

    with closing(_make_provider(db_path, times=(_T0, _T1))) as provider:
        assert provider.get_drawdown_fraction(_snapshot()) == 0.0
        assert provider.get_drawdown_fraction(_snapshot()) == 0.0

    assert _read_rows(db_path) == [
        (
            "TEST-ACCOUNT",
            "USD",
            100_000.0,
            _T0.isoformat(),
            _T0.isoformat(),
        ),
    ]


def test_higher_nav_advances_high_water_and_returns_zero(tmp_path):
    db_path = str(tmp_path / "drawdown.db")

    with closing(_make_provider(db_path, times=(_T0, _T1))) as provider:
        assert provider.get_drawdown_fraction(_snapshot(nav=100_000.0)) == 0.0
        assert provider.get_drawdown_fraction(_snapshot(nav=105_000.0)) == 0.0

    assert _read_rows(db_path) == [
        (
            "TEST-ACCOUNT",
            "USD",
            105_000.0,
            _T0.isoformat(),
            _T1.isoformat(),
        ),
    ]


def test_lower_nav_preserves_high_water_and_returns_exact_drawdown(
    tmp_path,
):
    db_path = str(tmp_path / "drawdown.db")

    with closing(_make_provider(db_path, times=(_T0, _T1))) as provider:
        assert provider.get_drawdown_fraction(_snapshot(nav=100_000.0)) == 0.0
        result = provider.get_drawdown_fraction(_snapshot(nav=95_000.0))

    assert result == pytest.approx(0.05)
    assert _read_rows(db_path) == [
        (
            "TEST-ACCOUNT",
            "USD",
            100_000.0,
            _T0.isoformat(),
            _T0.isoformat(),
        ),
    ]


def test_high_water_survives_provider_reinstantiation(tmp_path):
    db_path = str(tmp_path / "drawdown.db")

    with closing(_make_provider(db_path, times=(_T0,))) as first:
        assert first.get_drawdown_fraction(_snapshot(nav=100_000.0)) == 0.0

    with closing(_make_provider(db_path, times=(_T1,))) as second:
        result = second.get_drawdown_fraction(_snapshot(nav=95_000.0))

    assert result == pytest.approx(0.05)
    assert _read_rows(db_path) == [
        (
            "TEST-ACCOUNT",
            "USD",
            100_000.0,
            _T0.isoformat(),
            _T0.isoformat(),
        ),
    ]


def test_different_accounts_are_isolated_in_same_database(tmp_path):
    db_path = str(tmp_path / "drawdown.db")

    with (
        closing(
            _make_provider(
                db_path,
                account_id="ACCOUNT-A",
                times=(_T0, _T1),
            )
        ) as provider_a,
        closing(
            _make_provider(
                db_path,
                account_id="ACCOUNT-B",
                times=(_T0, _T1),
            )
        ) as provider_b,
    ):
        assert provider_a.get_drawdown_fraction(
            _snapshot(nav=100_000.0)
        ) == 0.0
        assert provider_b.get_drawdown_fraction(
            _snapshot(nav=50_000.0)
        ) == 0.0
        assert provider_a.get_drawdown_fraction(
            _snapshot(nav=90_000.0)
        ) == pytest.approx(0.10)
        assert provider_b.get_drawdown_fraction(
            _snapshot(nav=55_000.0)
        ) == 0.0

    assert _read_rows(db_path) == [
        (
            "ACCOUNT-A",
            "USD",
            100_000.0,
            _T0.isoformat(),
            _T0.isoformat(),
        ),
        (
            "ACCOUNT-B",
            "USD",
            55_000.0,
            _T0.isoformat(),
            _T1.isoformat(),
        ),
    ]


def test_currencies_maintain_separate_baselines(tmp_path):
    db_path = str(tmp_path / "drawdown.db")

    with closing(
        _make_provider(db_path, times=(_T0, _T1, _T2))
    ) as provider:
        assert provider.get_drawdown_fraction(
            _snapshot(nav=100_000.0, currency="USD")
        ) == 0.0
        assert provider.get_drawdown_fraction(
            _snapshot(nav=80_000.0, currency="EUR")
        ) == 0.0
        assert provider.get_drawdown_fraction(
            _snapshot(nav=95_000.0, currency="USD")
        ) == pytest.approx(0.05)

    assert _read_rows(db_path) == [
        (
            "TEST-ACCOUNT",
            "EUR",
            80_000.0,
            _T1.isoformat(),
            _T1.isoformat(),
        ),
        (
            "TEST-ACCOUNT",
            "USD",
            100_000.0,
            _T0.isoformat(),
            _T0.isoformat(),
        ),
    ]


def test_invalid_snapshot_evidence_fails_closed_without_mutation(tmp_path):
    db_path = str(tmp_path / "drawdown.db")

    with closing(_make_provider(db_path, times=(_T0, _T1))) as provider:
        assert provider.get_drawdown_fraction(_snapshot()) == 0.0
        baseline = _read_rows(db_path)
        assert len(baseline) == 1

        invalid_snapshots = [
            object(),
            None,
            {"nav": 100_000.0, "currency": "USD"},
        ]
        for invalid_nav in (
            None,
            True,
            "100000",
            math.nan,
            math.inf,
            -math.inf,
            0.0,
            -1.0,
        ):
            invalid_snapshots.append(_snapshot(nav=invalid_nav))
        for invalid_currency in (None, "", "US", "USDD", "U1D", 123):
            invalid_snapshots.append(_snapshot(currency=invalid_currency))

        for invalid_snapshot in invalid_snapshots:
            with pytest.raises(ValueError):
                provider.get_drawdown_fraction(invalid_snapshot)

    assert _read_rows(db_path) == baseline


def test_corrupt_stored_high_water_fails_closed_without_overwrite(
    tmp_path,
):
    corrupt_values = (0.0, -1.5, "not-a-number", float("inf"))

    for case_index, corrupt_value in enumerate(corrupt_values):
        db_path = str(tmp_path / f"corrupt_{case_index}.db")

        with closing(_make_provider(db_path, times=(_T0,))) as seeded:
            assert seeded.get_drawdown_fraction(_snapshot()) == 0.0
        _corrupt_column(db_path, "high_water_nav", corrupt_value)

        with closing(_make_provider(db_path, times=(_T1,))) as provider:
            with pytest.raises(ValueError):
                provider.get_drawdown_fraction(_snapshot(nav=105_000.0))

        rows = _read_rows(db_path)
        assert len(rows) == 1
        assert rows[0][2] == corrupt_value
        assert rows[0][3] == _T0.isoformat()
        assert rows[0][4] == _T0.isoformat()


def test_corrupt_stored_timestamps_fail_closed_without_overwrite(tmp_path):
    corrupt_values = ("", "not-a-timestamp", "2026-07-16T12:00:00")

    case_index = 0
    for column in ("initialized_at", "updated_at"):
        column_index = 3 if column == "initialized_at" else 4
        for corrupt_value in corrupt_values:
            db_path = str(tmp_path / f"ts_{case_index}.db")
            case_index += 1

            with closing(_make_provider(db_path, times=(_T0,))) as seeded:
                assert seeded.get_drawdown_fraction(_snapshot()) == 0.0
            _corrupt_column(db_path, column, corrupt_value)

            with closing(_make_provider(db_path, times=(_T1,))) as provider:
                with pytest.raises(ValueError):
                    provider.get_drawdown_fraction(
                        _snapshot(nav=105_000.0)
                    )

            rows = _read_rows(db_path)
            assert len(rows) == 1
            assert rows[0][2] == 100_000.0
            assert rows[0][column_index] == corrupt_value


def test_invalid_clock_evidence_never_creates_or_mutates_rows(tmp_path):
    invalid_clocks = (
        lambda: None,
        lambda: "2026-07-16T12:00:00Z",
        lambda: datetime(2026, 7, 16, 12, 0, 0),
    )

    for case_index, invalid_clock in enumerate(invalid_clocks):
        db_path = str(tmp_path / f"clock_{case_index}.db")
        with closing(
            _make_provider(db_path, clock=invalid_clock)
        ) as provider:
            with pytest.raises(ValueError):
                provider.get_drawdown_fraction(_snapshot())
        assert _read_rows(db_path) == []

    db_path = str(tmp_path / "clock_existing.db")
    with closing(_make_provider(db_path, times=(_T0,))) as good_provider:
        assert good_provider.get_drawdown_fraction(_snapshot()) == 0.0

    for invalid_clock in invalid_clocks:
        with closing(
            _make_provider(db_path, clock=invalid_clock)
        ) as provider:
            with pytest.raises(ValueError):
                provider.get_drawdown_fraction(_snapshot(nav=105_000.0))

    assert _read_rows(db_path) == [
        (
            "TEST-ACCOUNT",
            "USD",
            100_000.0,
            _T0.isoformat(),
            _T0.isoformat(),
        ),
    ]


def test_database_unavailable_raises_sqlite_error(tmp_path):
    blocked_path = tmp_path / "as-directory.db"
    blocked_path.mkdir()

    with pytest.raises(sqlite3.Error):
        with closing(_make_provider(str(blocked_path))) as provider:
            provider.get_drawdown_fraction(_snapshot())


def test_poisoned_schema_fails_without_migration_or_default(tmp_path):
    db_path = str(tmp_path / "poisoned.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE drawdown_high_water (wrong_column TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises((sqlite3.Error, ValueError)):
        with closing(_make_provider(db_path)) as provider:
            provider.get_drawdown_fraction(_snapshot())

    conn = sqlite3.connect(db_path)
    try:
        columns = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(drawdown_high_water)"
            )
        ]
    finally:
        conn.close()
    assert columns == ["wrong_column"]


def test_same_named_but_structurally_incompatible_schema_is_rejected(
    tmp_path,
):
    db_path = str(tmp_path / "same-named-incompatible.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE drawdown_high_water ("
            " account_id TEXT,"
            " account_currency TEXT,"
            " high_water_nav TEXT,"
            " initialized_at TEXT,"
            " updated_at TEXT"
            ")"
        )
        conn.commit()
        schema_before = conn.execute(
            "PRAGMA table_info(drawdown_high_water)"
        ).fetchall()
        indexes_before = conn.execute(
            "PRAGMA index_list(drawdown_high_water)"
        ).fetchall()
    finally:
        conn.close()

    assert [row[1] for row in schema_before] == [
        "account_id",
        "account_currency",
        "high_water_nav",
        "initialized_at",
        "updated_at",
    ]

    with pytest.raises((sqlite3.Error, ValueError)):
        with closing(_make_provider(db_path, times=(_T0,))) as provider:
            provider.get_drawdown_fraction(_snapshot())

    conn = sqlite3.connect(db_path)
    try:
        schema_after = conn.execute(
            "PRAGMA table_info(drawdown_high_water)"
        ).fetchall()
        indexes_after = conn.execute(
            "PRAGMA index_list(drawdown_high_water)"
        ).fetchall()
    finally:
        conn.close()

    assert schema_after == schema_before
    assert indexes_after == indexes_before
    assert _read_rows(db_path) == []


def test_incompatible_schema_rejection_does_not_change_journal_mode(
    tmp_path,
):
    db_path = str(tmp_path / "journal-mode.db")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute(
            "CREATE TABLE drawdown_high_water (wrong_column TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    conn = sqlite3.connect(db_path)
    try:
        mode_before = conn.execute("PRAGMA journal_mode").fetchone()[0]
        schema_before = conn.execute(
            "PRAGMA table_info(drawdown_high_water)"
        ).fetchall()
        indexes_before = conn.execute(
            "PRAGMA index_list(drawdown_high_water)"
        ).fetchall()
    finally:
        conn.close()

    assert mode_before == "delete"

    with pytest.raises((sqlite3.Error, ValueError)):
        with closing(_make_provider(db_path, times=(_T0,))) as provider:
            provider.get_drawdown_fraction(_snapshot())

    conn = sqlite3.connect(db_path)
    try:
        mode_after = conn.execute("PRAGMA journal_mode").fetchone()[0]
        schema_after = conn.execute(
            "PRAGMA table_info(drawdown_high_water)"
        ).fetchall()
        indexes_after = conn.execute(
            "PRAGMA index_list(drawdown_high_water)"
        ).fetchall()
    finally:
        conn.close()

    assert mode_after == "delete"
    assert schema_after == schema_before
    assert indexes_after == indexes_before
    assert _read_rows(db_path) == []


def test_multiple_instances_preserve_maximum_high_water(tmp_path):
    db_path = str(tmp_path / "shared.db")

    with (
        closing(_make_provider(db_path, times=(_T0, _T1, _T2))) as first,
        closing(_make_provider(db_path, times=(_T0, _T1, _T2))) as second,
    ):
        assert first.get_drawdown_fraction(_snapshot(nav=100_000.0)) == 0.0
        assert second.get_drawdown_fraction(_snapshot(nav=102_000.0)) == 0.0
        assert _read_rows(db_path)[0][2] == 102_000.0

        assert first.get_drawdown_fraction(
            _snapshot(nav=101_000.0)
        ) == pytest.approx(1_000.0 / 102_000.0)
        assert _read_rows(db_path)[0][2] == 102_000.0

        assert second.get_drawdown_fraction(
            _snapshot(nav=99_000.0)
        ) == pytest.approx(3_000.0 / 102_000.0)
        assert _read_rows(db_path)[0][2] == 102_000.0

        assert first.get_drawdown_fraction(_snapshot(nav=110_000.0)) == 0.0
        assert _read_rows(db_path)[0][2] == 110_000.0


def test_close_releases_connection_and_fails_deterministically_after(
    tmp_path,
):
    db_path = str(tmp_path / "drawdown.db")

    provider = _make_provider(db_path, times=(_T0,))
    assert provider.get_drawdown_fraction(_snapshot()) == 0.0
    provider.close()
    provider.close()

    with pytest.raises((sqlite3.Error, ValueError)):
        provider.get_drawdown_fraction(_snapshot(nav=105_000.0))

    assert _read_rows(db_path) == [
        (
            "TEST-ACCOUNT",
            "USD",
            100_000.0,
            _T0.isoformat(),
            _T0.isoformat(),
        ),
    ]


def test_contract_matches_runtime_resolver_expectations(tmp_path):
    db_path = str(tmp_path / "drawdown.db")

    with closing(_make_provider(db_path, times=(_T0, _T1))) as provider:
        parameters = list(
            inspect.signature(
                provider.get_drawdown_fraction
            ).parameters.values()
        )
        assert len(parameters) == 1
        assert parameters[0].default is inspect.Parameter.empty
        assert parameters[0].kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        )

        assert provider.get_drawdown_fraction(_snapshot(nav=100_000.0)) == 0.0
        value = provider.get_drawdown_fraction(_snapshot(nav=95_000.0))
        assert type(value) is float
        assert 0.0 <= value < 1.0
